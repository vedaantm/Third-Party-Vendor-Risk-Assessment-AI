import { render, screen } from "@testing-library/react";
import { truncatedAgents } from "../lib/agents";
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

describe("truncatedAgents", () => {
  test("uses truncated_agents when the backend provides it", () => {
    expect(truncatedAgents({ truncated_agents: ["strategic", "financial"], red_flags: [] })).toEqual([
      "financial",
      "strategic",
    ]);
  });

  test("falls back to the meta-agent's truncation red flag", () => {
    expect(
      truncatedAgents({
        red_flags: [
          "[FINANCIAL] Agent output was truncated — red flags may be incomplete",
          "[REPUTATION] Data breach reported [Reuters]",
          "[COMPLIANCE] Agent output was truncated — red flags may be incomplete",
        ],
      }),
    ).toEqual(["compliance", "financial"]);
  });

  test("an explicit empty list wins over red flags", () => {
    expect(
      truncatedAgents({ truncated_agents: [], red_flags: ["[FINANCIAL] Agent output was truncated — x"] }),
    ).toEqual([]);
  });

  test("no flags, no truncation", () => {
    expect(truncatedAgents({ red_flags: null })).toEqual([]);
  });
});
