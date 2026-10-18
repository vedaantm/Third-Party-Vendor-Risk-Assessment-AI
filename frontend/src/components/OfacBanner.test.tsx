import { render, screen } from "@testing-library/react";
import OfacBanner from "./OfacBanner";

const SDN = [
  "ATLAS HOLDING [SDGT] (SDN #28527) — exact name match, but the name is too short or generic to confirm",
  "ATLAS EXCHANGE [SDGT] [IFSR] (SDN #26536)",
];

function banner() {
  return screen.getByLabelText("OFAC sanctions screening");
}

test("clear: shown as clear, as a status (not an alert)", () => {
  render(<OfacBanner status="clear" matches={[]} />);
  expect(banner()).toHaveAttribute("data-ofac-tone", "clear");
  expect(screen.getByRole("status")).toHaveTextContent("OFAC screening: clear");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(banner()).not.toHaveTextContent(/manual screening/i);
});

test("match: shown as a sanctions match with the SDN entries listed", () => {
  render(<OfacBanner status="match" matches={["BANCO NACIONAL DE CUBA [CUBA] (SDN #306)"]} />);
  expect(screen.getByRole("alert")).toHaveTextContent("OFAC sanctions match");
  expect(screen.getByRole("alert")).toHaveTextContent("Do not engage this vendor");
  expect(banner()).toHaveAttribute("data-ofac-tone", "match");
  expect(screen.getByRole("list", { name: "OFAC SDN matches" })).toHaveTextContent("SDN #306");
});

test("possible_match: needs manual screening, never clear, lists matches", () => {
  render(<OfacBanner status="possible_match" matches={SDN} />);
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Needs manual screening — possible OFAC match");
  expect(alert).not.toHaveTextContent(/screening: clear/i);
  expect(banner()).toHaveAttribute("data-ofac-tone", "screen");
  const items = screen.getAllByRole("listitem");
  expect(items.map((li) => li.textContent)).toEqual(SDN);
});

test("unavailable: needs manual screening, never clear", () => {
  render(<OfacBanner status="unavailable" matches={[]} />);
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Needs manual screening — OFAC screening not completed");
  expect(alert).not.toHaveTextContent(/screening: clear/i);
  expect(banner()).toHaveAttribute("data-ofac-tone", "screen");
  expect(screen.queryByRole("list")).not.toBeInTheDocument();
});

test("null: needs manual screening, never clear", () => {
  render(<OfacBanner status={null} />);
  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("Needs manual screening — OFAC status not reported");
  expect(alert).not.toHaveTextContent(/screening: clear/i);
  expect(banner()).toHaveAttribute("data-ofac-tone", "screen");
});

test.each([undefined, "", "CLEAR", "cleared", "something_new"])(
  "unrecognised value %j is treated like null, never as clear",
  (status) => {
    render(<OfacBanner status={status} />);
    expect(banner()).toHaveAttribute("data-ofac-tone", "screen");
    expect(screen.getByRole("alert")).toHaveTextContent("Needs manual screening");
  },
);

test("null shows the caller's note when given", () => {
  render(<OfacBanner status={null} missingNote="Saved reports don't store the OFAC result." />);
  expect(screen.getByRole("alert")).toHaveTextContent("Saved reports don't store the OFAC result.");
});
