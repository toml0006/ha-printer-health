import { rootRoute } from "./routes/__root";
import { indexRoute } from "./routes/index";
import { printersRoute } from "./routes/printers";
import { discoveryRoute } from "./routes/discovery";
import { cardsRoute } from "./routes/cards";
import { templatesRoute } from "./routes/templates";
import { configRoute } from "./routes/config";
import { helpRoute } from "./routes/help";

export const routeTree = rootRoute.addChildren([
  indexRoute,
  printersRoute,
  discoveryRoute,
  cardsRoute,
  templatesRoute,
  configRoute,
  helpRoute,
]);
