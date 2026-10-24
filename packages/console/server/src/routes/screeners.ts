import type { FastifyInstance } from "fastify";
import type {
  CreditSpreadPayload,
  EarningsCalendarPayload,
  IncomeList,
  IncomeListPayload,
  EarningsRecordPayload,
  SignalBoardPayload,
  SkatePayload,
  SkateRecordPayload,
} from "@console/shared";
import type { ConsoleConfig } from "../config.js";
import { readEarningsCalendar } from "../readers/earningsCalendar.js";
import { readEarningsRecord } from "../readers/earningsRecord.js";
import { CREDIT_SPREADS, INCOME_LISTS, readCreditSpreadList, readIncomeList } from "../readers/incomeLists.js";
import { readSignalBoard } from "../readers/signalBoard.js";
import { readSkate } from "../readers/skate.js";
import { readSkateRecord } from "../readers/skateRecord.js";

export function registerScreenerRoutes(app: FastifyInstance, config: ConsoleConfig): void {
  // Our own lists (technicals income-lists). The vendor's saved lists are no longer served: its
  // subscription ended 2026-10-24.
  app.get<{ Params: { list: string }; Querystring: { day?: string } }>(
    "/api/screeners/own/:list",
    async (req, reply): Promise<IncomeListPayload | CreditSpreadPayload | undefined> => {
      if (req.params.list === CREDIT_SPREADS) return readCreditSpreadList(config, req.query.day ?? null);
      const which = req.params.list as IncomeList;
      if (!INCOME_LISTS.includes(which)) {
        await reply.code(404).send({ error: `no list of ours named ${req.params.list}` });
        return undefined;
      }
      return readIncomeList(config, which, req.query.day ?? null);
    },
  );
  app.get<{ Querystring: { day?: string } }>(
    "/api/screeners/board",
    async (req): Promise<SignalBoardPayload> => readSignalBoard(config, req.query.day ?? null),
  );
  app.get<{ Querystring: { day?: string } }>(
    "/api/screeners/skate",
    async (req): Promise<SkatePayload> => readSkate(config, req.query.day ?? null),
  );
  // The forward record: one file for the whole screen, not a session's (docs/skate-plan.md step 4).
  app.get("/api/screeners/skate/record", async (): Promise<SkateRecordPayload> => readSkateRecord(config));
  app.get("/api/screeners/earnings-record", async (): Promise<EarningsRecordPayload> => readEarningsRecord(config));
  app.get<{ Querystring: { day?: string } }>(
    "/api/screeners/earnings",
    async (req): Promise<EarningsCalendarPayload> => readEarningsCalendar(config, req.query.day ?? null),
  );
}
