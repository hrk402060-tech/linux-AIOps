import { act, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { AuthUser } from '../api/auth';
import { getCurrentUser } from '../api/auth';
import { useCurrentUser } from './useCurrentUser';

vi.mock('../api/auth', () => ({ getCurrentUser: vi.fn() }));
const user: AuthUser = {
  id: 'scoped-user', username: 'scoped-user', display_name: null,
  role: 'operator', enabled: true, must_change_password: false,
  asset_scope: 'assigned', access_revision: 1, created_at: '', last_login_at: null,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function Harness({ reset }: { reset: () => void }) {
  const { currentUser, authLoading, setCurrentUser } = useCurrentUser(reset);
  return <><p>{authLoading ? 'checking' : currentUser ? `user:${currentUser.access_revision}` : 'signed-out'}</p>
    <button onClick={() => setCurrentUser({ ...user, id: 'new-user', access_revision: 5 })}>login</button>
    <button onClick={() => setCurrentUser(null)}>logout</button></>;
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getCurrentUser).mockResolvedValue(user);
});
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it('refreshes current permissions on returning to the window', async () => {
  const reset = vi.fn();
  render(<Harness reset={reset} />);
  await screen.findByText('user:1');
  vi.mocked(getCurrentUser).mockResolvedValue({ ...user, access_revision: 2 });
  act(() => window.dispatchEvent(new Event('focus')));
  await screen.findByText('user:2');
  expect(reset).toHaveBeenCalledTimes(1);
});

it('checks a visible session every thirty seconds', async () => {
  vi.useFakeTimers();
  render(<Harness reset={vi.fn()} />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('user:1')).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(30_000); });
  expect(getCurrentUser).toHaveBeenCalledTimes(2);
});

it('clears a disabled or revoked session and stops background requests when signed out', async () => {
  const reset = vi.fn();
  render(<Harness reset={reset} />);
  await screen.findByText('user:1');
  vi.mocked(getCurrentUser).mockRejectedValue(new Error('session revoked'));
  act(() => window.dispatchEvent(new Event('focus')));
  await screen.findByText('signed-out');
  expect(reset).toHaveBeenCalledTimes(1);
  act(() => window.dispatchEvent(new Event('focus')));
  expect(getCurrentUser).toHaveBeenCalledTimes(2);
});

it('does not restore a stale response after expiry or a different login', async () => {
  const pending = deferred<AuthUser>();
  vi.mocked(getCurrentUser).mockReturnValue(pending.promise);
  render(<Harness reset={vi.fn()} />);
  act(() => window.dispatchEvent(new Event('sentinel:session-expired')));
  expect(screen.getByText('signed-out')).toBeInTheDocument();
  act(() => screen.getByRole('button', { name: 'login' }).click());
  await act(async () => pending.resolve(user));
  expect(screen.getByText('user:5')).toBeInTheDocument();
});

it('cleans up polling and focus listeners when unmounted', async () => {
  const view = render(<Harness reset={vi.fn()} />);
  await screen.findByText('user:1');
  view.unmount();
  act(() => window.dispatchEvent(new Event('focus')));
  await waitFor(() => expect(getCurrentUser).toHaveBeenCalledTimes(1));
});
