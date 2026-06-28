import { createOrder } from "./orders/index";

router.post("/orders", (_request, response) => {
  response.json(createOrder("42", [1000]));
});
