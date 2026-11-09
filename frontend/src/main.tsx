import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';

import App from './App';
import './styles.css';
import './ai-agent.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#167a5b',
          colorInfo: '#167a5b',
          colorBorder: '#d9dedb',
          colorBgLayout: '#f3f5f4',
          borderRadius: 6,
          fontFamily: "Inter, 'Noto Sans SC', 'Microsoft YaHei', sans-serif",
        },
        components: {
          Button: { controlHeight: 36 },
          Table: { headerBg: '#f7f8f7', headerColor: '#4b5550' },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
