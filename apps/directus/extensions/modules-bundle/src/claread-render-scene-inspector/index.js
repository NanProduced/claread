import InspectorModule from "./module.vue";

export default {
  id: "claread-render-scene-inspector",
  name: "Render Scene Inspector",
  icon: "visibility",
  routes: [
    {
      path: "",
      component: InspectorModule,
    },
  ],
};
