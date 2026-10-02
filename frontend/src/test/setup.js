import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Node >= 25 ships its own global localStorage/sessionStorage (undefined localStorage without
// --localstorage-file), which shadows jsdom's. Tests must use the browser implementations.
const browser = globalThis.jsdom?.window;
if (browser) {
  for (const name of ["localStorage", "sessionStorage"]) {
    Object.defineProperty(globalThis, name, {
      value: browser[name],
      configurable: true,
      writable: true,
    });
  }
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
});
