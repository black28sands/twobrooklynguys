import { defineCollection, z } from 'astro:content';

const episodes = defineCollection({
  type: 'content',
  schema: z.object({
    title: z.string(),
    episode: z.number(),
    date: z.string(),
    duration: z.string(),
    audioUrl: z.string(),
    videoUrl: z.string().optional(),
    description: z.string(),
    chapters: z.array(z.object({
      title: z.string(),
      start: z.string(),
    })).optional(),
    tags: z.array(z.string()).optional(),
  }),
});

export const collections = { episodes };
