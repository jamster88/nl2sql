import { describe, expect, it } from "vitest";

import {
  bandScale,
  extentDomain,
  labelStride,
  linearScale,
  niceStep,
  ticksFor,
  valueDomain,
} from "../src/charts/scale";

describe("niceStep", () => {
  it("only ever returns 1, 2 or 5 times a power of ten", () => {
    for (const span of [0.3, 7, 43, 910, 12345, 6.7e6]) {
      const step = niceStep(span, 5);
      const mantissa = step / 10 ** Math.floor(Math.log10(step));
      expect([1, 2, 5, 10]).toContain(Number(mantissa.toPrecision(6)));
    }
  });

  it("gives a usable step for a degenerate span or count", () => {
    expect(niceStep(0, 5)).toBe(1);
    expect(niceStep(-10, 5)).toBe(1);
    expect(niceStep(10, 0)).toBe(1);
  });

  it("picks each of the four mantissas as the rough step moves through them", () => {
    expect(niceStep(10, 10)).toBe(1); // rough 1
    expect(niceStep(18, 10)).toBe(2); // rough 1.8
    expect(niceStep(45, 10)).toBe(5); // rough 4.5
    expect(niceStep(90, 10)).toBe(10); // rough 9
  });
});

describe("ticksFor", () => {
  it("covers the domain in round numbers", () => {
    expect(ticksFor(0, 100, 5)).toEqual([0, 20, 40, 60, 80, 100]);
  });

  it("keeps the last tick despite float drift", () => {
    // 0.1 + 0.1 + 0.1 overshoots 0.3 by 5.5e-17 without the epsilon.
    expect(ticksFor(0, 0.3, 3)).toContain(0.3);
  });

  it("returns the single value for an empty domain", () => {
    expect(ticksFor(7, 7)).toEqual([7]);
  });

  it("returns nothing for a domain that is not finite", () => {
    expect(ticksFor(Number.NaN, 10)).toEqual([]);
    expect(ticksFor(0, Number.POSITIVE_INFINITY)).toEqual([]);
  });
});

describe("linearScale", () => {
  it("maps the domain onto the range", () => {
    const scale = linearScale([0, 10], [0, 100]);
    expect(scale(0)).toBe(0);
    expect(scale(5)).toBe(50);
    expect(scale(10)).toBe(100);
  });

  it("inverts when the range does, which is what an SVG y axis needs", () => {
    const scale = linearScale([0, 10], [200, 0]);
    expect(scale(0)).toBe(200);
    expect(scale(10)).toBe(0);
  });

  it("puts a zero-width domain in the middle of the range rather than dividing by zero", () => {
    const scale = linearScale([5, 5], [0, 100]);
    expect(scale(5)).toBe(50);
    expect(Number.isFinite(scale(5))).toBe(true);
  });

  it("carries its domain, range and ticks", () => {
    const scale = linearScale([0, 4], [0, 40], 4);
    expect(scale.domain).toEqual([0, 4]);
    expect(scale.range).toEqual([0, 40]);
    expect(scale.ticks).toEqual([0, 1, 2, 3, 4]);
  });
});

describe("valueDomain", () => {
  it("anchors at zero so bars are comparable", () => {
    expect(valueDomain([120, 200, 180])[0]).toBe(0);
  });

  it("rounds the top up to a whole step", () => {
    const [, top] = valueDomain([0, 187]);
    expect(top).toBe(200);
  });

  it("extends below zero only when the data goes there", () => {
    const [bottom, top] = valueDomain([-40, 90]);
    expect(bottom).toBeLessThan(0);
    expect(top).toBeGreaterThanOrEqual(90);
  });

  it("handles a single value, positive, negative and zero", () => {
    expect(valueDomain([5])).toEqual([0, 6]);
    expect(valueDomain([-5])).toEqual([-6, 0]);
    expect(valueDomain([0])).toEqual([0, 1]);
  });

  it("falls back to 0..1 with nothing to scale", () => {
    expect(valueDomain([])).toEqual([0, 1]);
  });
});

describe("extentDomain", () => {
  it("pads both ends so no mark sits on the frame", () => {
    const [min, max] = extentDomain([10, 20]);
    expect(min).toBeLessThan(10);
    expect(max).toBeGreaterThan(20);
  });

  it("gives a single value room on both sides", () => {
    expect(extentDomain([7])).toEqual([6, 8]);
  });

  it("falls back to 0..1 when empty", () => {
    expect(extentDomain([])).toEqual([0, 1]);
  });
});

describe("bandScale", () => {
  it("spaces bands evenly with a gap between them", () => {
    const band = bandScale(4, 400);
    expect(band.step).toBe(100);
    expect(band.bandwidth).toBe(75);
    expect(band(0)).toBe(12.5);
    expect(band.center(0)).toBe(50);
    expect(band.center(3)).toBe(350);
  });

  it("fills the slot when padding is zero, which is what a line wants", () => {
    const band = bandScale(4, 400, 0);
    expect(band.bandwidth).toBe(100);
  });

  it("never produces a bandwidth of zero", () => {
    expect(bandScale(0, 400).bandwidth).toBeGreaterThan(0);
    expect(bandScale(10000, 10).bandwidth).toBeGreaterThanOrEqual(1);
  });
});

describe("labelStride", () => {
  it("labels every band when they are wide enough", () => {
    expect(labelStride(8, 640)).toBe(1);
  });

  it("thins rather than letting labels collide", () => {
    expect(labelStride(80, 640)).toBeGreaterThan(1);
  });

  it("is 1 for a single band", () => {
    expect(labelStride(1, 640)).toBe(1);
    expect(labelStride(0, 640)).toBe(1);
  });

  it("copes with a plot narrower than one label", () => {
    expect(labelStride(100, 10)).toBeGreaterThan(1);
  });
});
