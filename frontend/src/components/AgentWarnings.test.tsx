import { render, screen } from "@testing-library/react";
import { truncatedAgentsFromRedFlags } from "../lib/agents";
import AgentWarnings from "./AgentWarnings";

test("renders nothing when every analysis completed in full", () => {
  const { container } = render(<AgentWarnings unavailable={[]} truncated={[]} />);
  expect(container).toBeEmptyDOMElement();
});

test("unavailable agents are shown as a visible warning", () => {
  render(<AgentWarnings unavailable={["financial", "operational"]} truncated={[]} />);
  const warning = screen.getByTestId("unavailable-agents");
  expect(warning).toHaveAttribute("role", "alert");
  expect(warning).toHaveTextContent("2 analyses did not complete: Financial, Operational");
  expect(warning).toHaveTextContent("needs human review");
  expect(screen.queryByTestId("truncated-agents")).not.toBeInTheDocument();
});

test("a single unavailable agent uses singular wording", () => {
  render(<AgentWarnings unavailable={["strategic"]} truncated={[]} />);
  expect(screen.getByTestId("unavailable-agents")).toHaveTextContent("1 analysis did not complete: Strategic fit");
});

test("truncated agents are shown as a visible warning", () => {
  render(<AgentWarnings unavailable={[]} truncated={["reputation"]} />);
  const warning = screen.getByTestId("truncated-agents");
  expect(warning).toHaveAttribute("role", "alert");
  expect(warning).toHaveTextContent("Output cut off for: Reputation");
  expect(warning).toHaveTextContent("red flags and evidence after the cut-off may be missing");
});

test("both warnings show together", () => {
  render(<AgentWarnings unavailable={["compliance"]} truncated={["financial"]} />);
  expect(screen.getAllByRole("alert")).toHaveLength(2);
});

describe("truncatedAgentsFromRedFlags (saved reports and pre-field job results)", () => {
  test("reads the meta-agent's truncation red flag", () => {
    expect(
      truncatedAgentsFromRedFlags([
        "[FINANCIAL] Agent output was truncated — red flags may be incomplete",
        "[REPUTATION] Data breach reported [Reuters]",
        "[COMPLIANCE] Agent output was truncated — red flags may be incomplete",
      ]),
    ).toEqual(["compliance", "financial"]);
  });

  test("no flags, no truncation", () => {
    expect(truncatedAgentsFromRedFlags(null)).toEqual([]);
    expect(truncatedAgentsFromRedFlags(["[REPUTATION] Data breach reported"])).toEqual([]);
  });
});
