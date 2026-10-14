import { useState } from "react";
import { DealCard, withDisplayCase } from "./App.jsx";
import fixture from "../tests/fixtures/deals.json";

// The real card from App.jsx, fed the same eight fixture records the
// Playwright tests use, prepared the way the app prepares loaded records.
const deal = (prefix) => withDisplayCase(fixture.deals.find(d => d.id.startsWith(prefix)));
const chowFreshness = "CMS CHOW file: Jul 17, 2026 (quarterly)";

function Card({ record, open = false }) {
  const [expanded, setExpanded] = useState(open);
  return (
    <DealCard deal={record} expanded={expanded} onToggle={() => setExpanded(e => !e)}
      searchForms={fixture.ucc_search_forms} onEntity={() => {}} history={expanded ? [] : null}
      facilities={fixture.facilities} chowFreshness={chowFreshness} />
  );
}

export default {
  title: "Feed/Deal card",
  component: DealCard,
  render: (args) => <Card {...args} />,
};

export const CmsConfirmedSale = { args: { record: deal("fe24935c") } };
export const PendingNewsSale = { args: { record: deal("40bb281b") } };
export const OldConFilingNoSeller = { args: { record: deal("84695e99") } };
export const UndisclosedBuyer = { args: { record: deal("6b6a97b7") } };
export const LeaseNoNamedParties = { args: { record: deal("0a013a42") } };
export const ControlChange = { args: { record: deal("335c39ed") } };
export const NewsAcquisitionFinancingNoBorrower = { args: { record: deal("fd9f37e5") } };
export const IndividualUccFiling = { args: { record: deal("a5ebee98") } };
export const DetailsOpen = { args: { record: deal("40bb281b"), open: true } };
