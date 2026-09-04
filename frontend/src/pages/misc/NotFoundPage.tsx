import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useEffect } from 'react';

export function NotFoundPage() {
  const navigate = useNavigate();
  useEffect(() => {
    document.title = 'Not found · Sentinel Gujarat';
  }, []);
  return (
    <Result
      status="404"
      title="Page not found"
      subTitle="The page you requested does not exist or was moved."
      extra={
        <Button type="primary" onClick={() => navigate('/dashboard')}>
          Back to dashboard
        </Button>
      }
    />
  );
}
