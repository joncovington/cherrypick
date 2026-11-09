// Module hooks for demo_console_nokeyring.mjs: any import of the keyring binding resolves to an empty one.
const EMPTY =
  "data:text/javascript," +
  encodeURIComponent(
    "export class Entry { getPassword() { return null; } setPassword() {} deletePassword() { return false; } }" +
      "export default { Entry };",
  );

export async function resolve(specifier, context, nextResolve) {
  if (specifier === "@napi-rs/keyring") return { url: EMPTY, shortCircuit: true };
  return nextResolve(specifier, context);
}
