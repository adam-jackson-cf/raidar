import { describe, expect, it } from "bun:test";
import { renderHomepage } from "../src/homepage";

describe("homepage starter", () => {
  it("contains semantic landmarks", () => {
    const html = renderHomepage();
    expect(html).toContain("<header");
    expect(html).toContain("<main");
    expect(html).toContain("<footer");
  });

  it("contains required section ids", () => {
    const html = renderHomepage();
    expect(html).toContain('id=\"hero\"');
    expect(html).toContain('id=\"features\"');
    expect(html).toContain('id=\"proof\"');
    expect(html).toContain('id=\"cta\"');
  });

  it("contains primary CTA marker", () => {
    const html = renderHomepage();
    expect(html).toContain('data-testid=\"primary-cta\"');
  });
});
