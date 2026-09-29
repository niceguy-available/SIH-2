import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'sonner';
import Home from './pages/Home';
import './App.css';
const client = new QueryClient({defaultOptions:{queries:{refetchOnWindowFocus:false}}});
export default function App() {
  return <QueryClientProvider client={client}><BrowserRouter><Routes><Route path="*" element={<Home />} /></Routes></BrowserRouter><Toaster theme="dark" richColors closeButton /></QueryClientProvider>;
}