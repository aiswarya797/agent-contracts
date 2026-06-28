import { createOrder } from "../src/orders/index";

test("creates an order", () => {
  expect(createOrder("42", [500, 500]).total).toBe(1000);
});
