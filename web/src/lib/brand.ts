/**
 * Build-time product brand. `NEXT_PUBLIC_BRAND=deepx` builds the Deep X
 * white-label; anything else is the stock Onyx product. Enterprise settings
 * (application name, custom logo) still take precedence where they are set.
 */

export type BrandId = "onyx" | "deepx";

export interface Brand {
  id: BrandId;
  /** Product name shown in the UI wherever "Onyx" would show. */
  name: string;
  /** Name of the Slack/Discord bot in UI copy. */
  botName: string;
  supportEmail: string;
  /** Favicon path under `web/public`. */
  favicon: string;
  /** Show the "Powered by Onyx" line under the sidebar logo. */
  showPoweredBy: boolean;
  /** Community support link; null hides the "Need help?" line. */
  communityUrl: string | null;
}

const BRANDS: Record<BrandId, Brand> = {
  onyx: {
    id: "onyx",
    name: "Onyx",
    botName: "OnyxBot",
    supportEmail: "support@onyx.app",
    favicon: "/onyx.ico",
    showPoweredBy: true,
    communityUrl: "https://discord.gg/4NA5SbzrWb",
  },
  deepx: {
    id: "deepx",
    name: "Deep X",
    botName: "Deep X Bot",
    supportEmail:
      process.env.NEXT_PUBLIC_BRAND_SUPPORT_EMAIL || "support@deepxroute.com",
    favicon: "/deepx.svg",
    showPoweredBy: false,
    communityUrl: null,
  },
};

export function resolveBrand(id: string | undefined): Brand {
  return id === "deepx" ? BRANDS.deepx : BRANDS.onyx;
}

export const BRAND: Brand = resolveBrand(process.env.NEXT_PUBLIC_BRAND);

export type MessageTree = { [key: string]: string | MessageTree };

/**
 * Rewrites the stock product name in UI message values for a white-label
 * brand. Keys are unchanged. The stock brand returns the same tree.
 */
export function brandMessages(
  tree: MessageTree,
  brand: Brand = BRAND
): MessageTree {
  if (brand.id === "onyx") return tree;
  const rewrite = (text: string): string =>
    text
      .split("support@onyx.app")
      .join(brand.supportEmail)
      .split("OnyxBot")
      .join(brand.botName)
      .split("Onyx")
      .join(brand.name);
  const walk = (node: MessageTree): MessageTree =>
    Object.fromEntries(
      Object.entries(node).map(([key, value]) => [
        key,
        typeof value === "string" ? rewrite(value) : walk(value),
      ])
    );
  return walk(tree);
}
