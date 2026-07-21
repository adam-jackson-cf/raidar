export interface LedgerEntry {
	id: string;
	kind: "credit" | "debit";
	amountCents: number;
}

export function isValidEntry(entry: LedgerEntry): boolean {
	return (
		entry.id.trim().length > 0 &&
		Number.isInteger(entry.amountCents) &&
		entry.amountCents > 0
	);
}

export function ledgerBalanceCents(entries: LedgerEntry[]): number {
	let balance = 0;
	for (const entry of entries) {
		if (!isValidEntry(entry)) {
			throw new Error(`Invalid ledger entry: ${entry.id}`);
		}
		balance += entry.amountCents;
	}
	return balance;
}

export function formatBalance(balanceCents: number): string {
	const sign = balanceCents < 0 ? "-" : "";
	const absolute = Math.abs(balanceCents);
	const dollars = Math.floor(absolute / 100);
	const cents = `${absolute % 100}`.padStart(2, "0");
	return `${sign}$${dollars}.${cents}`;
}
