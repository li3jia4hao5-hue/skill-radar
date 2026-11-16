# Try the local scanner

From the repository root, run:

```bash
python scripts/radar.py scan --config examples/scan-config.json --out work/demo-profile.json
```

This command scans the included fictional project without a network request. Its generated profile includes resolved local paths, so keep it in the ignored `work/` directory. It is a scanner demonstration, not an end-to-end recommendation or benchmark.

Next, ask your host agent to read `SKILL.md`, inspect the profile, formulate generic GitHub queries, verify real candidates, and produce an assessment using `references/workflow.md`. The repo includes no fabricated live rankings or third-party skill snapshots.
