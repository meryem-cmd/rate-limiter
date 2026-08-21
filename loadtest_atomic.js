import http from 'k6/http';
import { Counter } from 'k6/metrics';

export const options = {
  vus: 15,
  duration: '5s',
};

const allowCount = new Counter('allow_count');
const denyCount = new Counter('deny_count');

export default function () {
  const res = http.get('http://127.0.0.1:8000/check/test-client-2');
  if (res.status === 200) {
    allowCount.add(1);
  } else if (res.status === 429) {
    denyCount.add(1);
  }
}