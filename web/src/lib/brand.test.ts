import { brandMessages, resolveBrand } from "@/lib/brand";
import englishMessages from "@/i18n/messages/en.json";

const DEEPX = resolveBrand("deepx");
const ONYX = resolveBrand(undefined);

function collectValues(node: unknown): string[] {
  if (typeof node === "string") return [node];
  if (node && typeof node === "object") {
    return Object.values(node).flatMap(collectValues);
  }
  return [];
}

describe("resolveBrand", () => {
  it("defaults to the stock Onyx brand", () => {
    expect(ONYX.name).toBe("Onyx");
    expect(resolveBrand("unknown").id).toBe("onyx");
  });

  it("selects Deep X and hides the powered-by line", () => {
    expect(DEEPX.name).toBe("Deep X");
    expect(DEEPX.showPoweredBy).toBe(false);
  });
});

describe("brandMessages", () => {
  const tree = {
    connect: { title: "Connect to Onyx" },
    bot: "Configure OnyxBot for Slack",
    help: "Reach the Onyx team at support@onyx.app",
    tryOnyxButton: { label: "Try Onyx while waiting" },
  };

  it("returns the same tree for the stock brand", () => {
    expect(brandMessages(tree, ONYX)).toBe(tree);
  });

  it("rewrites values but not keys for Deep X", () => {
    expect(brandMessages(tree, DEEPX)).toEqual({
      connect: { title: "Connect to Deep X" },
      bot: "Configure Deep X Bot for Slack",
      help: `Reach the Deep X team at ${DEEPX.supportEmail}`,
      tryOnyxButton: { label: "Try Deep X while waiting" },
    });
  });

  it("does not mutate the input tree", () => {
    const before = JSON.stringify(tree);
    brandMessages(tree, DEEPX);
    expect(JSON.stringify(tree)).toBe(before);
  });

  it("leaves no Onyx name in the English catalog", () => {
    const leftovers = collectValues(
      brandMessages(englishMessages, DEEPX)
    ).filter((value) => value.includes("Onyx"));
    expect(leftovers).toEqual([]);
  });
});
