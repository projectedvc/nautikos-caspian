export async function GET() {
  return Response.json({
    connected: Boolean(process.env.CDSE_CLIENT_ID && process.env.CDSE_CLIENT_SECRET),
  });
}
