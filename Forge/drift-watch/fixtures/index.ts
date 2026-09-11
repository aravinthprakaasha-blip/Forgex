declare const process: {
	env: Record<string, string | undefined>;
};

const databaseUrl = process.env.DATABASE_URL;

const apiKey = process.env["API_KEY"];

const port = process.env.PORT;

const debug = process.env["DEBUG"];

const paymentSecret = process.env.PAYMENTS_WEBHOOK_SECRET;
