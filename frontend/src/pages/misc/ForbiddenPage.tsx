import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useEffect } from 'react';

export function ForbiddenPage() {
  const navigate = useNavigate();
  useEffect(() => {
    document.title = 'Access denied · Sentinel Gujarat';
  }, []);
  return (
    <Result
      status="403"
      title="Access denied"
      subTitle="Your role does not include permission for this page. Ask an administrator if you need access."
      extra={
        <Button type="primary" onClick={() => navigate('/dashboard')}>
          Back to dashboard
        </Button>
      }
    />
  );
}
