import {
	type LedgerEntry,
	formatBalance,
	isValidEntry,
	ledgerBalanceCents,
} from "@/lib/ledger";
import { describe, expect, it } from "vitest";

function entry(overrides: Partial<LedgerEntry>): LedgerEntry {
	return {
		id: "txn-1",
		kind: "credit",
		amountCents: 1000,
		...overrides,
	};
}

describe("ledgerBalanceCents", () => {
	it("sums credit entries", () => {
		expect(
			ledgerBalanceCents([
				entry({ id: "txn-1", amountCents: 1250 }),
				entry({ id: "txn-2", amountCents: 750 }),
			]),
		).toBe(2000);
	});

	it("returns zero for an empty ledger", () => {
		expect(ledgerBalanceCents([])).toBe(0);
	});

	it("rejects invalid entries", () => {
		expect(() =>
			ledgerBalanceCents([entry({ id: " ", amountCents: 100 })]),
		).toThrow("Invalid ledger entry");
	});

	// Bug report RAID-1042: debit entries are added to the balance instead of
	// subtracted. Re-enable this reproduction test as part of the fix.
	it.skip("subtracts debit entries from the balance", () => {
		expect(
			ledgerBalanceCents([
				entry({ id: "txn-1", kind: "credit", amountCents: 5000 }),
				entry({ id: "txn-2", kind: "debit", amountCents: 1500 }),
			]),
		).toBe(3500);
	});
});

describe("isValidEntry", () => {
	it("accepts a positive integer credit entry", () => {
		expect(isValidEntry(entry({}))).toBe(true);
	});

	it("rejects blank ids, fractional amounts, and non-positive amounts", () => {
		expect(isValidEntry(entry({ id: "  " }))).toBe(false);
		expect(isValidEntry(entry({ amountCents: 10.5 }))).toBe(false);
		expect(isValidEntry(entry({ amountCents: 0 }))).toBe(false);
	});
});

describe("formatBalance", () => {
	it("formats positive balances as dollars and cents", () => {
		expect(formatBalance(123456)).toBe("$1234.56");
	});

	it("pads single-digit cents", () => {
		expect(formatBalance(105)).toBe("$1.05");
	});

	it("formats negative balances with a leading sign", () => {
		expect(formatBalance(-250)).toBe("-$2.50");
	});
});
