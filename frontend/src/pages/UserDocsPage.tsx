import { useEffect } from 'react';
import { Button } from 'antd';
import { ArrowLeft, BookOpen } from 'lucide-react';
import { PlatformUserDocs } from '../components/PlatformUserDocs';
import { openDocumentedPage, platformPageHref } from '../utils/userDocsNavigation';

export function UserDocsPage() {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = '使用文档 · Linux 智能综合运维平台';
    return () => { document.title = previousTitle; };
  }, []);
  return <div className="user-docs-page">
    <header className="user-docs-page-header">
      <div className="user-docs-page-heading">
        <BookOpen size={26} />
        <div><p>Linux 智能综合运维平台</p><h1>使用文档</h1></div>
      </div>
      <Button href={platformPageHref()} icon={<ArrowLeft size={16} />}>返回平台</Button>
    </header>
    <main className="user-docs-page-content">
      <PlatformUserDocs onNavigate={openDocumentedPage} />
    </main>
  </div>;
}
