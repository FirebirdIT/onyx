import { render, screen } from "@tests/setup/test-utils";
import { StreamingHeader } from "@/app/app/message/messageComponents/timeline/headers/StreamingHeader";
import { ElapsedTime } from "@/app/app/message/messageComponents/timeline/headers/ElapsedTime";

const NOW = 1_800_000_000_000;

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(NOW);
});

afterEach(() => jest.useRealTimers());

function renderHeader(isExpanded: boolean, collapsible = true) {
  return render(
    <StreamingHeader
      headerText="Searching"
      collapsible={collapsible}
      isExpanded={isExpanded}
      onToggle={jest.fn()}
      streamingStartTime={NOW - 12_000}
    />
  );
}

describe("StreamingHeader", () => {
  it.each([true, false])(
    "shows the running time when expanded is %s",
    async (isExpanded) => {
      renderHeader(isExpanded);
      expect(await screen.findByText("12s")).toBeInTheDocument();
      expect(screen.getByRole("button")).toHaveAttribute(
        "aria-expanded",
        String(isExpanded)
      );
    }
  );

  it("shows the running time when the header cannot collapse", async () => {
    renderHeader(false, false);
    expect(await screen.findByText("12s")).toBeInTheDocument();
  });
});

describe("ElapsedTime", () => {
  it("counts up while the answer runs", async () => {
    render(<ElapsedTime streamingStartTime={NOW - 65_000} />);
    expect(await screen.findByText("1m 5s")).toBeInTheDocument();
  });

  it("shows nothing before the answer starts", () => {
    const { container } = render(<ElapsedTime />);
    expect(container).toBeEmptyDOMElement();
  });
});
