import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { jsonResponse, mockApi, renderAt } from "../test/utils.jsx";
import LoginPage from "./LoginPage.jsx";

describe("LoginPage", () => {
  it("signs in, stores the token for the session and redirects", async () => {
    const calls = mockApi({ "POST /auth/login": { access_token: "abc", token_type: "bearer" } });
    renderAt("/login", <LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "alice@proofstack.dev");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery-staple");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("projects page")).toBeInTheDocument();
    expect(sessionStorage.getItem("proofstack.access_token")).toBe("abc");
    expect(localStorage.getItem("proofstack.access_token")).toBeNull();
    expect(JSON.parse(calls[0].options.body)).toEqual({
      email: "alice@proofstack.dev",
      password: "correct-horse-battery-staple",
    });
  });

  it("shows the API's error detail on failure", async () => {
    mockApi({
      "POST /auth/login": jsonResponse(
        { title: "Unauthorized", detail: "Invalid email or password." },
        401,
      ),
    });
    renderAt("/login", <LoginPage />);
    await userEvent.type(screen.getByLabelText("Email"), "alice@proofstack.dev");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password-123");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(sessionStorage.getItem("proofstack.access_token")).toBeNull();
  });

  it("registers then signs in", async () => {
    const calls = mockApi({
      "POST /auth/register": jsonResponse({ id: "u1" }, 201),
      "POST /auth/login": { access_token: "new" },
    });
    renderAt("/login", <LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: "Create one" }));
    await userEvent.type(screen.getByLabelText("Email"), "bob@proofstack.dev");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery-staple");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    await waitFor(() => expect(sessionStorage.getItem("proofstack.access_token")).toBe("new"));
    expect(calls.map((c) => c.path)).toEqual(["/auth/register", "/auth/login"]);
  });
});
