const http = require('http');
const fs = require('fs');
const path = require('path');

const root = __dirname;
const statePath = path.join(root, 'state.json');
const html = fs.readFileSync(path.join(root, 'index.html'));
const readState = () => JSON.parse(fs.readFileSync(statePath, 'utf8'));
const writeState = state => fs.writeFileSync(statePath, JSON.stringify(state));
const body = req => new Promise((resolve, reject) => {
  let value = '';
  req.on('data', chunk => value += chunk);
  req.on('end', () => { try { resolve(value ? JSON.parse(value) : {}); } catch (error) { reject(error); } });
});
const send = (res, code, value, type = 'application/json') => {
  res.writeHead(code, {'content-type': type, 'cache-control': 'no-store'});
  res.end(type === 'application/json' ? JSON.stringify(value) : value);
};

const server = http.createServer(async (req, res) => {
  try {
    if (req.method === 'GET' && req.url === '/') return send(res, 200, html, 'text/html; charset=utf-8');
    if (req.method === 'GET' && req.url === '/api/state') return send(res, 200, readState());
    if (req.method !== 'POST') return send(res, 404, {error: 'not found'});
    const state = readState();
    const data = await body(req);
    if (req.url === '/api/search') {
      if (data.query !== 'QCU-217') return send(res, 400, {error: 'ticket not found'});
      state.events.push('search:QCU-217');
    } else if (req.url === '/api/select') {
      if (state.events.at(-1) !== 'search:QCU-217') return send(res, 409, {error: 'search first'});
      state.selected = true; state.events.push('select:QCU-217');
    } else if (req.url === '/api/save') {
      if (!state.selected) return send(res, 409, {error: 'select first'});
      state.assignee = data.assignee; state.priority = data.priority; state.note = data.note;
      state.events.push('save');
    } else if (req.url === '/api/ready') {
      if (state.events.at(-1) !== 'save') return send(res, 409, {error: 'save first'});
      if (state.assignee !== 'Maya Chen' || state.priority !== 'High' || state.note !== 'Validated in staging') {
        return send(res, 409, {error: 'details incomplete'});
      }
      state.status = 'Ready'; state.events.push('ready');
    } else return send(res, 404, {error: 'not found'});
    writeState(state); return send(res, 200, state);
  } catch (error) { return send(res, 500, {error: String(error)}); }
});

server.listen(Number(process.env.CP_HOST_PORT), '127.0.0.1', () => {
  if (process.send) process.send({ready: true});
});
