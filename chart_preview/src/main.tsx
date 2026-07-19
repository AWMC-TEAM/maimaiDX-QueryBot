import '@mantine/core/styles.css';
import { MantineProvider, createTheme } from '@mantine/core';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import './index.css';
import App from './App';

const theme = createTheme({});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark">
      {/* HashRouter：便于 bot 用本地静态服务托管 /#/record 录制页 */}
      <HashRouter>
        <App />
      </HashRouter>
    </MantineProvider>
  </StrictMode>
);
