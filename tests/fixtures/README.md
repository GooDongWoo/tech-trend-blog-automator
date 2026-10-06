# Fixture queue

## Mechanism
The queue persists pending jobs before acknowledging them.

## API
Call `enqueue(job)` to persist work; call `ack(job_id)` after completion.

## Limits
A failed worker leaves the job pending for retry. No throughput benchmark was run.
