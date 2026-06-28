import { formatUser } from "../users/index";
import { totalCents } from "./internal/totals";

export function createOrder(userId: string, cents: number[]) {
  return {
    user: formatUser(userId),
    total: totalCents(cents)
  };
}
