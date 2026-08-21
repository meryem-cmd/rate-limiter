import http from 'k6/http';
import { Counter } from 'k6/metrics';

export const options = {
  scenarios: {
    sustained_test: {
      executor: 'constant-arrival-rate',
      rate: 250,              // more conservative target
      timeUnit: '1s',
      duration: '15s',
      preAllocatedVUs: 100,
      maxVUs: 300,
    },
  },
};
const allowCount = new Counter('allow_count');
const denyCount = new Counter('deny_count');

export default function () {
  const clientId = Math.floor(Math.random() * 50);
  const res = http.get(`http://127.0.0.1:8000/check/load-client-${clientId}`);
  if (res.status === 200) {
    allowCount.add(1);
  } else if (res.status === 429) {
    denyCount.add(1);
  }
}