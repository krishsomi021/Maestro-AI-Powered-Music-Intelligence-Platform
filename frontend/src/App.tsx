import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { AgentPage } from './pages/Agent';

function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<AgentPage />} />
      </Route>
    </Routes>
  );
}

export default App;
