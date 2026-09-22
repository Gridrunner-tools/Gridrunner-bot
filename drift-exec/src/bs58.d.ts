// bs58 v6 ships no bundled TypeScript declarations; declare the subset we use.
declare module 'bs58' {
  export function decode(input: string): Uint8Array;
  export function encode(input: Uint8Array | number[]): string;
  const _default: { decode: typeof decode; encode: typeof encode };
  export default _default;
}
