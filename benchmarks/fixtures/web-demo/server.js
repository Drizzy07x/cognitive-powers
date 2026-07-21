const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const port = Number(process.env.COGNITIVE_WEB_BENCHMARK_PORT || 41791);
const page = fs.readFileSync(path.join(__dirname, 'src', 'index.html'));

const server = http.createServer((request, response) => {
  if (request.url !== '/') {
    response.writeHead(404).end('Not found');
    return;
  }
  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  response.end(page);
});

server.listen(port, '127.0.0.1');
