import type { MetadataRoute } from "next";
import {
  aboutRoute,
  blogRoute,
  dailyRoute,
  helpRoute,
  homeRoute,
  shareDemoRoute,
} from "@/lib/routes";

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  const baseUrl = "https://claread.com";

  return [
    {
      url: `${baseUrl}${homeRoute}`,
      lastModified,
      changeFrequency: "weekly",
      priority: 1,
    },
    {
      url: `${baseUrl}${dailyRoute}`,
      lastModified,
      changeFrequency: "daily",
      priority: 0.9,
    },
    {
      url: `${baseUrl}${aboutRoute}`,
      lastModified,
      changeFrequency: "monthly",
      priority: 0.5,
    },
    {
      url: `${baseUrl}${helpRoute}`,
      lastModified,
      changeFrequency: "monthly",
      priority: 0.5,
    },
    {
      url: `${baseUrl}${blogRoute}`,
      lastModified,
      changeFrequency: "weekly",
      priority: 0.4,
    },
    {
      url: `${baseUrl}${shareDemoRoute}`,
      lastModified,
      changeFrequency: "weekly",
      priority: 0.3,
    },
  ];
}
