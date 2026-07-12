import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.removeAttribute("data-mode");
  document.documentElement.removeAttribute("data-density");
  document.documentElement.removeAttribute("data-motion");
});
