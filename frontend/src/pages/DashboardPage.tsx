import { DashboardPreviewPage } from './DashboardPreviewPage';
import type { PageKey } from '../App';

export function DashboardPage({
  onNavigate, onViewFindings, onOpenTerminal,
}: {
  onNavigate: (page: PageKey) => void;
  onViewFindings?: (assetId: string) => void;
  onOpenTerminal?: (assetId: string) => void;
}) {
  return <DashboardPreviewPage
    onNavigate={onNavigate}
    onViewFindings={onViewFindings}
    onOpenTerminal={onOpenTerminal}
    preview={false}
  />;
}
