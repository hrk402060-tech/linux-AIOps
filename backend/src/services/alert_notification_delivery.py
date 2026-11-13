import asyncio
import ipaddress
from datetime import timedelta
from email.message import EmailMessage
import logging
from queue import Empty, Queue
import smtplib
import socket
import ssl
from threading import BoundedSemaphore, Event, Thread, Timer
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from sqlalchemy import or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from src.models.alert_notification import AlertNotification, AlertNotificationConfig
from src.services import alert_notification_config as config
from src.services.alert_notifications import _audit, _commit, utc_now
from src.services.crypto import CredentialCryptoError, CredentialVault

logger = logging.getLogger(__name__)
SMTP_TIMEOUT_SECONDS = 5
SMTP_TOTAL_TIMEOUT_SECONDS = 90
DNS_TIMEOUT_SECONDS = 3
_DNS_SLOTS = BoundedSemaphore(4)
LEASE_SECONDS = 180
WORKER_INTERVAL_SECONDS = 5
MAX_BATCH_SIZE = 10


class NotificationDeliveryError(RuntimeError):
    pass


def _resolve_host(host: str) -> str:
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if not _DNS_SLOTS.acquire(blocking=False):
            raise NotificationDeliveryError('smtp_dns_busy') from None
    result: Queue[str | None] = Queue(maxsize=1)

    def resolve() -> None:
        try:
            values = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
            result.put(str(values[0][4][0]) if values else None)
        except OSError:
            result.put(None)
        finally:
            _DNS_SLOTS.release()

    Thread(target=resolve, daemon=True).start()
    try:
        address = result.get(timeout=DNS_TIMEOUT_SECONDS)
    except Empty as exc:
        raise NotificationDeliveryError('smtp_dns_timeout') from exc
    if address is None:
        raise NotificationDeliveryError('smtp_dns_unavailable')
    return address


def _recover_leases(session: Session) -> None:
    now = utc_now()
    expired = (AlertNotification.status == 'sending', AlertNotification.lease_expires_at <= now)
    session.execute(update(AlertNotification).where(*expired, AlertNotification.attempts < AlertNotification.max_attempts)
                    .values(status='retry', error_code='worker_interrupted', claim_token=None, lease_expires_at=None, next_attempt_at=now)
                    .execution_options(synchronize_session=False))
    session.execute(update(AlertNotification).where(*expired, AlertNotification.attempts >= AlertNotification.max_attempts)
                    .values(status='failed', error_code='worker_interrupted', claim_token=None, lease_expires_at=None, next_attempt_at=None)
                    .execution_options(synchronize_session=False))


def claim_next(session: Session) -> tuple[AlertNotification, str] | None:
    try:
        _recover_leases(session)
        channel = config.get_record(session)
        if channel is None or not channel.enabled or not channel.recipients:
            session.execute(update(AlertNotification).where(AlertNotification.status.in_(('pending', 'retry')))
                            .values(status='blocked', error_code='notification_disabled' if channel and not channel.enabled
                                    else 'notification_not_configured', next_attempt_at=None))
            _commit(session)
            return None
        now = utc_now()
        due = (AlertNotification.status.in_(('pending', 'retry')),
               AlertNotification.attempts < AlertNotification.max_attempts,
               or_(AlertNotification.next_attempt_at.is_(None), AlertNotification.next_attempt_at <= now))
        item = session.scalar(select(AlertNotification).where(*due).order_by(
            AlertNotification.created_at, AlertNotification.id).with_for_update(skip_locked=True).limit(1))
        if item is None:
            _commit(session)
            return None
        token = str(uuid4())
        changed = session.execute(update(AlertNotification).where(AlertNotification.id == item.id, *due).values(
            status='sending', claim_token=token, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            attempts=AlertNotification.attempts + 1, recipients=item.recipients or list(channel.recipients),
            error_code=None, next_attempt_at=None,
        ).execution_options(synchronize_session=False)).rowcount
        _commit(session)
        if not changed:
            return None
        session.refresh(item)
        return item, token
    except SQLAlchemyError:
        session.rollback()
        logger.warning('Notification claim failed')
        raise


def finalize(session: Session, notification_id: str, token: str, error_code: str | None) -> bool:
    item = session.get(AlertNotification, notification_id)
    if item is None:
        return False
    now = utc_now()
    if error_code is None:
        changes = {'status': 'sent', 'sent_at': now, 'error_code': None, 'next_attempt_at': None}
    else:
        exhausted = item.attempts >= item.max_attempts
        changes = {'status': 'failed' if exhausted else 'retry', 'error_code': error_code,
                   'next_attempt_at': None if exhausted else now + timedelta(seconds=min(30 * 2 ** (item.attempts - 1), 600))}
    changed = session.execute(update(AlertNotification).where(
        AlertNotification.id == item.id, AlertNotification.status == 'sending',
        AlertNotification.claim_token == token, AlertNotification.lease_expires_at > now,
    ).values(**changes, claim_token=None, lease_expires_at=None).execution_options(synchronize_session=False)).rowcount
    if changed:
        _audit(session, item.id, 'delivery_' + str(changes['status']), 'system',
               'SMTP 已接受全部接收人；不代表已读' if error_code is None else '投递失败；保留累计次数与安全错误码')
    _commit(session)
    return bool(changed)


def _block_claim(session: Session, notification_id: str, token: str) -> None:
    session.execute(update(AlertNotification).where(
        AlertNotification.id == notification_id, AlertNotification.status == 'sending', AlertNotification.claim_token == token,
        AlertNotification.lease_expires_at > utc_now(),
    ).values(status='blocked', error_code='notification_disabled', claim_token=None, lease_expires_at=None,
             next_attempt_at=None, attempts=AlertNotification.attempts - 1).execution_options(synchronize_session=False))
    _commit(session)


def _password(channel: AlertNotificationConfig, vault: CredentialVault) -> str:
    if channel.encrypted_password is None:
        return ''
    if channel.password_nonce is None or channel.key_version is None:
        raise NotificationDeliveryError('smtp_secret_unavailable')
    try:
        return vault.decrypt(channel.encrypted_password, channel.password_nonce, channel.id,
                             config.SECRET_TYPE, channel.key_version)
    except (CredentialCryptoError, InvalidTag, UnicodeDecodeError) as exc:
        raise NotificationDeliveryError('smtp_secret_unavailable') from exc


def send_smtp(
    channel: AlertNotificationConfig, item: AlertNotification, vault: CredentialVault, stop: Event | None = None,
) -> None:
    """Resolve with a bounded budget; TLS verifies the original host, not its IP."""
    if channel.security not in {'starttls', 'ssl'}:
        raise NotificationDeliveryError('smtp_tls_required')
    if not item.recipients:
        raise NotificationDeliveryError('smtp_recipients_missing')
    message = EmailMessage()
    message['Subject'], message['From'] = item.subject, channel.sender
    message['To'] = ', '.join(item.recipients)
    message['Message-ID'] = f'<{item.id}@linuxops.invalid>'
    message.set_content(item.body)
    context = ssl.create_default_context()
    client: smtplib.SMTP | None = None
    timed_out = Event()

    def abort() -> None:
        timed_out.set()
        if client is not None and client.sock is not None:
            try:
                client.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                logger.warning('SMTP timeout socket shutdown failed')
            client.close()

    timer = Timer(SMTP_TOTAL_TIMEOUT_SECONDS, abort)
    timer.daemon = True
    timer.start()
    finished = Event()

    def watch_stop() -> None:
        while not finished.wait(0.1):
            if stop is not None and stop.is_set():
                abort()
                return

    if stop is not None:
        Thread(target=watch_stop, daemon=True).start()
    try:
        password = _password(channel, vault)
        address = _resolve_host(channel.host)
        if stop is not None and stop.is_set():
            raise NotificationDeliveryError('worker_stopped')
        if channel.security == 'ssl':
            client = smtplib.SMTP_SSL(timeout=SMTP_TIMEOUT_SECONDS, context=context, local_hostname='linuxops.invalid')
        else:
            client = smtplib.SMTP(timeout=SMTP_TIMEOUT_SECONDS, local_hostname='linuxops.invalid')
        client._host = channel.host
        client.connect(address, channel.port)
        if timed_out.is_set() or (stop is not None and stop.is_set()):
            raise NotificationDeliveryError('worker_stopped' if stop and stop.is_set() else 'smtp_total_timeout')
        if channel.security == 'starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        if channel.username:
            client.login(channel.username, password)
        if timed_out.is_set():
            raise NotificationDeliveryError('smtp_total_timeout')
        refused = client.send_message(message, from_addr=channel.sender, to_addrs=item.recipients)
        if timed_out.is_set():
            raise NotificationDeliveryError('smtp_total_timeout')
        if refused:
            raise NotificationDeliveryError('smtp_recipients_refused')
    except smtplib.SMTPAuthenticationError as exc:
        raise NotificationDeliveryError('smtp_authentication_failed') from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise NotificationDeliveryError('smtp_recipients_refused') from exc
    except (ssl.SSLError, smtplib.SMTPNotSupportedError) as exc:
        raise NotificationDeliveryError('smtp_tls_failed') from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise NotificationDeliveryError('smtp_unavailable') from exc
    finally:
        finished.set()
        timer.cancel()
        if client is not None:
            if timed_out.is_set() or (stop is not None and stop.is_set()):
                client.close()
            else:
                try:
                    client.quit()
                except (smtplib.SMTPException, OSError):
                    client.close()


def deliver_batch(session_factory: sessionmaker[Session], vault: CredentialVault, stop: Event | None = None) -> int:
    """Claim and send on a worker thread; each iteration has its own DB session."""
    count = 0
    for _ in range(MAX_BATCH_SIZE):
        if stop is not None and stop.is_set():
            break
        with session_factory() as session:
            claim = claim_next(session)
            if claim is None:
                break
            item, token = claim
            channel = config.get_record(session)
            if stop is not None and stop.is_set():
                finalize(session, item.id, token, 'worker_stopped')
                break
            if channel is None or not channel.enabled:
                _block_claim(session, item.id, token)
                continue
            error_code = None
            try:
                send_smtp(channel, item, vault, stop)
            except NotificationDeliveryError as exc:
                error_code = str(exc)
                logger.warning('Notification delivery %s failed: %s', item.id, error_code)
            except Exception as exc:
                error_code = 'notification_internal_error'
                logger.warning('Notification delivery %s failed: %s', item.id, type(exc).__name__)
            finalize(session, item.id, token, error_code)
            count += 1
    return count


async def run_scheduler(
    session_factory: sessionmaker[Session], vault: CredentialVault, stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await _run_batch_until_stop(session_factory, vault, stop_event)
        except Exception as exc:
            logger.warning('Notification worker iteration failed: %s', type(exc).__name__)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=WORKER_INTERVAL_SECONDS)
        except TimeoutError:
            continue


async def _run_batch_until_stop(
    session_factory: sessionmaker[Session], vault: CredentialVault, stop_event: asyncio.Event,
) -> None:
    thread_stop = Event()
    batch = asyncio.create_task(asyncio.to_thread(deliver_batch, session_factory, vault, thread_stop))
    stopping = asyncio.create_task(stop_event.wait())
    try:
        done, _ = await asyncio.wait((batch, stopping), return_when=asyncio.FIRST_COMPLETED)
        if stopping in done:
            thread_stop.set()
        await batch
    finally:
        thread_stop.set()
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
