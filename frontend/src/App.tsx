import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { JobMonitoringPage } from './pages/JobMonitoringPage';
import { QueueStatisticsPage } from './pages/QueueStatisticsPage';
import { RecommendationsPage } from './pages/RecommendationsPage';
import { JobHistoryPage } from './pages/JobHistoryPage';

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<JobMonitoringPage />} />
        <Route path="queue-stats" element={<QueueStatisticsPage />} />
        <Route path="recommendations" element={<RecommendationsPage />} />
        <Route path="history" element={<JobHistoryPage />} />
      </Route>
    </Routes>
  );
}

export default App;
