import { describe, expect, it } from "vitest";

import { counted, plainText } from "../src/api/text";

describe("plainText", () => {
  it("undoes the three entities html.escape writes", () => {
    expect(plainText("Thrift &amp; Table")).toBe("Thrift & Table");
    expect(plainText("&lt;none&gt;")).toBe("<none>");
  });

  it("unescapes the ampersand last, so an escaped entity stays literal", () => {
    expect(plainText("&amp;lt;")).toBe("&lt;");
  });

  it("leaves every other entity alone", () => {
    expect(plainText("&copy; &quot;")).toBe("&copy; &quot;");
  });
});

describe("counted", () => {
  it("agrees the noun with the count", () => {
    expect(counted(0, "correction")).toBe("0 corrections");
    expect(counted(1, "correction")).toBe("1 correction");
    expect(counted(2, "correction")).toBe("2 corrections");
  });

  it("takes an irregular plural", () => {
    expect(counted(3, "query", "queries")).toBe("3 queries");
  });
});
