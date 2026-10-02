import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, mockApi, renderAt, signIn } from "../test/utils.jsx";
import ProjectsPage from "./ProjectsPage.jsx";

describe("ProjectsPage", () => {
  it("lists projects and creates a new one", async () => {
    signIn();
    let projects = [{ id: "p1", name: "Shop API", visibility: "private" }];
    const calls = mockApi({
      "GET /projects": () => ({ items: projects, next_cursor: null }),
      "POST /projects": (options) => {
        const body = JSON.parse(options.body);
        projects = [...projects, { id: "p2", ...body }];
        return jsonResponse(projects[1], 201);
      },
    });
    renderAt("/projects-under-test", <ProjectsPage />);
    expect(await screen.findByRole("link", { name: "Shop API" })).toHaveAttribute(
      "href",
      "/projects/p1",
    );
    await userEvent.type(screen.getByLabelText("Name"), "Billing");
    await userEvent.selectOptions(screen.getByLabelText("Visibility"), "public");
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(await screen.findByRole("link", { name: "Billing" })).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST");
    expect(JSON.parse(post.options.body)).toEqual({ name: "Billing", visibility: "public" });
    expect(post.options.headers.Authorization).toBe("Bearer test-token");
  });

  it("signs out when the token is rejected", async () => {
    signIn("expired");
    mockApi({ "GET /projects": jsonResponse({ title: "Unauthorized" }, 401) });
    renderAt("/projects-under-test", <ProjectsPage />);
    await waitFor(() => expect(sessionStorage.getItem("proofstack.access_token")).toBeNull());
  });
});
