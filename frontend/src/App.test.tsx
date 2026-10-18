import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

test("renders the navigation", () => {
  render(
    <MemoryRouter>
      <App />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: "Evaluate" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "History" })).toBeInTheDocument();
});
