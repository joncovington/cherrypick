/** Worker entry: one `readFlow` over `workerData`, posted back, then exit. See `gexFlowCache.ts`. */

import { parentPort, workerData } from "node:worker_threads";
import { readFlow, type FlowJob } from "./gexFlowRead.js";

parentPort?.postMessage(readFlow(workerData as FlowJob));
