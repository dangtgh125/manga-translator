import { describe, expect, it } from "vitest";
import { errorMessage } from "./api";

describe("errorMessage", () => {
  it("returns strings as-is", () => { expect(errorMessage("x")).toBe("x"); });
  it("joins structured errors", () => {
    expect(errorMessage({ message: "no valid images", errors: ["a: bad", "b: big"] })).toBe("no valid images: a: bad; b: big");
  });
  it("falls back for unknown shapes", () => { expect(errorMessage(null)).toBe("request failed"); });
});
