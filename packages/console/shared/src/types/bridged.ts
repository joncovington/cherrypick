/**
 * The envelope every module-CLI bridge hands on: `data` is the module's own JSON, or null with the
 * reason it could not be read. A failed read is never an empty result -- a subprocess that did not
 * run and a module that answered "nothing yet" are different facts, and a page shows which.
 */
export interface BridgedResult<T> {
  ok: boolean;
  data: T | null;
  error: string | null;
}
