import { useEffect, useRef, useState } from 'react';
import { getPlatformPreferences, type PlatformPreferences } from '../api/system';
import type { PageKey } from '../App';
import { requestedPlatformPage } from '../utils/userDocsNavigation';
import { applyPlatformPreferences, defaultPlatformPreferences } from '../utils/platformPreferences';

export function usePlatformPreferences(userId: string, selectedPage: PageKey, onNavigate: (page: PageKey) => void) {
  const [platform, setPlatform] = useState<PlatformPreferences>(defaultPlatformPreferences);
  const latestPage = useRef(selectedPage);
  latestPage.current = selectedPage;
  useEffect(() => {
    let active = true;
    let hasSavedPage = true;
    try {
      const memory = JSON.parse(localStorage.getItem(`sentinel_navigation:${userId}`) ?? '{}') as { activePage?: string };
      hasSavedPage = Boolean(memory.activePage);
    } catch (cause) { console.warn(cause instanceof Error ? cause.message : '导航偏好不可用'); }
    const load = async (initial = false) => {
      try {
        const result = await getPlatformPreferences();
        if (!active) return;
        setPlatform(result); applyPlatformPreferences(result);
        if (initial && !hasSavedPage && !requestedPlatformPage() && latestPage.current === 'dashboard') onNavigate(result.default_page);
      } catch (cause) {
        if (active) console.warn(cause instanceof Error ? cause.message : '平台显示配置加载失败');
      }
    };
    void load(true);
    const changed = () => void load();
    window.addEventListener('sentinel:preferences-changed', changed);
    return () => { active = false; window.removeEventListener('sentinel:preferences-changed', changed); };
  }, [userId]);
  return platform;
}
