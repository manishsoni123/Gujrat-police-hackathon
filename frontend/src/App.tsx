import { ConfigProvider, App as AntApp, message } from 'antd';
import enGB from 'antd/locale/en_GB';
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from 'react-router-dom';
import { antTheme } from './theme/tokens';
import { router } from './router';
import { AuthBootstrap } from './auth/AuthProvider';
import { ApiError } from './api/client';

/** One QueryClient: mutations surface the server `detail` as a toast; queries render inline errors. */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
      staleTime: 15_000,
      refetchOnWindowFocus: false,
    },
  },
  mutationCache: new MutationCache({
    onError: (err) => {
      if (err instanceof ApiError && err.status === 401) return;
      message.error(err instanceof Error ? err.message : 'Request failed');
    },
  }),
  queryCache: new QueryCache(),
});

export function App() {
  return (
    <ConfigProvider theme={antTheme} locale={enGB}>
      <AntApp>
        <QueryClientProvider client={queryClient}>
          <AuthBootstrap>
            <RouterProvider router={router} />
          </AuthBootstrap>
        </QueryClientProvider>
      </AntApp>
    </ConfigProvider>
  );
}
