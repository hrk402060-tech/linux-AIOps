import { afterEach, describe, expect, it, vi } from 'vitest';

import { openTerminalTab, terminalAssetHref } from './terminalNavigation';

afterEach(() => vi.restoreAllMocks());

describe('terminal navigation', () => {
  it('uses only the selected asset ID and URL-encodes it as one parameter', () => {
    const previous = window.location.href;
    window.history.replaceState(null, '', '/console/?ticket=old&token=stale#previous');
    try {
      const assetId = 'asset/1?ticket=secret&value=two';
      const url = new URL(terminalAssetHref(assetId), window.location.origin);
      expect(url.pathname).toBe('/console/');
      expect([...url.searchParams.keys()]).toEqual(['terminal_asset']);
      expect(url.searchParams.get('terminal_asset')).toBe(assetId);
      expect(url.hash).toBe('');
    } finally {
      window.history.replaceState(null, '', previous);
    }
  });

  it('opens a safe native link without relying on a popup return value', () => {
    const open = vi.spyOn(window, 'open');
    let clicked: HTMLAnchorElement | undefined;
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked = this;
      expect(document.body.contains(this)).toBe(true);
    });
    openTerminalTab('node-1');
    expect(clicked?.getAttribute('href')).toBe(`${window.location.origin}/?terminal_asset=node-1`);
    expect(clicked?.target).toBe('_blank');
    expect(clicked?.rel).toBe('noopener noreferrer');
    expect(document.body.contains(clicked ?? null)).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });

  it('keeps a double-slash pathname on the platform origin', () => {
    const previous = window.location.href;
    window.history.replaceState(null, '', `${window.location.origin}//redirect.example/console`);
    try {
      const url = new URL(terminalAssetHref('node-1'), window.location.origin);
      expect(url.origin).toBe(window.location.origin);
      expect(url.pathname).toBe('//redirect.example/console');
    } finally {
      window.history.replaceState(null, '', previous);
    }
  });

  it('removes the temporary link even if browser navigation throws', () => {
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      throw new Error('Navigation unavailable');
    });
    expect(() => openTerminalTab('node-1')).toThrow('Navigation unavailable');
    expect(document.querySelector('a[target="_blank"]')).not.toBeInTheDocument();
  });
});
