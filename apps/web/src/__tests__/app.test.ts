import { describe, expect, it } from "vitest";
import { formatSubsystemBadge } from "../App";

describe("@apex-one/web helpers", () => {
  it("maps subsystem states accurately without inflating unverified status", () => {
    expect(formatSubsystemBadge(true)).toBe("PASS");
    expect(formatSubsystemBadge(false)).toBe("CONDITIONAL");
    expect(formatSubsystemBadge(false, "BLOCKED")).toBe("BLOCKED");
  });
});
