import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";
import { ROUTER_FUTURE } from "./routerFuture";

test("renders the navigation", () => {
  render(
    <MemoryRouter future={ROUTER_FUTURE}>
      <App />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: "Evaluate" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "History" })).toBeInTheDocument();
});
