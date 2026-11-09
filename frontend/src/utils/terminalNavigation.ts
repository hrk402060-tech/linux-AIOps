export function terminalAssetHref(assetId: string): string {
  return `${window.location.origin}${window.location.pathname}?${new URLSearchParams({ terminal_asset: assetId })}`;
}

export function openTerminalTab(assetId: string): void {
  const link = document.createElement('a');
  link.href = terminalAssetHref(assetId);
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  link.hidden = true;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
  }
}
