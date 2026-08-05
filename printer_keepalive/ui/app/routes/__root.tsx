import { createRootRoute, Outlet } from "@tanstack/react-router";
import { useIsFetching } from "@tanstack/react-query";
import { CmykStripe } from "@/components/cmyk-stripe";
import { Topbar } from "@/components/topbar";
import { TabNav } from "@/components/tab-nav";
import { Toaster } from "@/components/toast";
import { useHealth } from "@/lib/queries";

function RootLayout() {
  const { data } = useHealth();
  const isFetching = useIsFetching();

  return (
    <>
      <CmykStripe />
      <div className={`fetch-bar${isFetching ? " active" : ""}`} />
      <div className="container">
        <Topbar version={data?.version} />
        <TabNav />
        <Outlet />
      </div>
      <Toaster />
    </>
  );
}

export const rootRoute = createRootRoute({
  component: RootLayout,
});
