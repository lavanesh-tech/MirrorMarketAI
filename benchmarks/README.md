# Benchmarks

- `k6/`: load-test scripts (k6). `workload.js` is the request mix, `lib.js` creates the
  synthetic users, workspaces, products, documents and prices it runs against.
- `results/`: one JSON record per recorded run (`k6-<profile>-<label>.json`) with the
  commit, machine, configuration and measured numbers. `docs/PERFORMANCE.md` is generated
  from these files.
- Component benchmarks (retrieval, agents, Redis, Kafka, WebSockets) live in
  `backend/benchmarks/` with their own result files.

```bash
make load-up                 # the stack, configured for load testing
make load-smoke              # 30 s: does every scripted request work?
make load-test               # 2 min at RATE=30 requests/s
make load-capacity           # stepped: STEPS=25,50,100,150,200,300
make load-up API_WORKERS=4   # the same with 4 API processes, then run again
make up                      # back to the normal settings
```

Every recorded result includes the date, git commit, dataset and its size, environment,
hardware, configuration, metric and value. Synthetic data is labelled as synthetic.
Numbers are never estimated or invented.

A run adds synthetic users (`load-...@example.com`) and their workspaces to the local
database. `make reset-db` removes everything.
