import { createElement } from "react";
export interface Product {
  product_id: string;
  name: string;
  brand: string;
  category: string;
  price_inr: number;
  price: string;
  summary: string;
  final_sale: boolean;
  attributes: Record<string, string | number | boolean>;
  description?: string;
  available?: number;
  in_stock?: boolean;
}

export const ATTR_LABEL: Record<string, string> = {
  type: "Type",
  wireless: "Wireless",
  battery_hours: "Battery",
  noise_cancelling: "Noise cancelling",
  weight_g: "Weight",
  water_resistance: "Water resistance",
};

export function attrText(k: string, v: unknown): string {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (k === "battery_hours") return `${v} h`;
  if (k === "weight_g") return `${v} g`;
  if (k.endsWith("_w")) return `${v} W`;
  return String(v);
}

export function label(k: string) {
  return ATTR_LABEL[k] ?? k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

export function Stock({ available }: { available?: number }) {
  if (available === undefined) return null;
  if (available <= 0) return createElement("span", { className: "stock-out small" }, "Out of stock");
  if (available <= 3) return createElement("span", { className: "stock-low small" }, `Only ${available} left`);
  return createElement("span", { className: "stock-in small" }, `In stock (${available})`);
}

export const titleCase = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
